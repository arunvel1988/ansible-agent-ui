import subprocess
import tempfile
import yaml
import re
from langchain_ollama import OllamaLLM

llm = OllamaLLM(model="llama3")

chat_history = []


# ---------------- CLEAN LLM OUTPUT ----------------
def clean_yaml_output(text):
    text = text.strip()

    if "```" in text:
        parts = text.split("```")
        for part in parts:
            if "- name:" in part:
                text = part.replace("yaml", "").strip()
                break

    if "- name:" in text:
        text = text[text.index("- name:"):]

    return text.strip()


# ---------------- INTENT EXTRACTION ----------------
def extract_user_task(user_input):
    match = re.search(r"user\s+(\w+)", user_input.lower())
    username = match.group(1) if match else "testuser"

    return {
        "name": f"Create user {username}",
        "user": {
            "name": username,
            "password": "{{ '123' | password_hash('sha512') }}",
            "groups": "sudo",
            "append": True
        }
    }


# ---------------- SANITIZER ----------------
def sanitize_playbook(playbook, user_input):
    play = playbook[0]

    clean_play = {
        "name": play.get("name", "Play"),
        "hosts": "localhost",
        "become": True
    }

    tasks = play.get("tasks", [])
    clean_tasks = []

    for task in tasks:
        if not isinstance(task, dict):
            continue

        new_task = {"name": task.get("name", "Task")}

        for key, value in task.items():
            if key == "name":
                continue

            # -------- USER FIX --------
            if key == "user" and isinstance(value, dict):
                if "sudo" in value:
                    value.pop("sudo")
                    value["groups"] = "sudo"
                    value["append"] = True

                if "password" in value:
                    value["password"] = "{{ '123' | password_hash('sha512') }}"

                new_task["user"] = value

            # -------- COMMAND FIX --------
            elif key in ["command", "shell"]:
                new_task[key] = value
                new_task["register"] = "cmd_output"

            else:
                new_task[key] = value

        # keep valid tasks only
        if len(new_task) > 1:
            clean_tasks.append(new_task)

    # 🚨 NEVER allow empty tasks
    if not clean_tasks:
        clean_tasks.append(extract_user_task(user_input))

    # show output if command used
    if any("command" in t or "shell" in t for t in clean_tasks):
        clean_tasks.append({
            "name": "Show command output",
            "debug": {"var": "cmd_output.stdout"}
        })

    clean_play["tasks"] = clean_tasks

    return [clean_play]


# ---------------- PARSE ----------------
def parse_yaml(text):
    try:
        data = yaml.safe_load(text)
        if not isinstance(data, list):
            return None, "Playbook must start with '- name:'"
        return data, None
    except Exception as e:
        return None, str(e)


# ---------------- SAFETY ----------------
def is_safe(text):
    blocked = ["rm -rf", "shutdown", "reboot", "mkfs", "dd if="]
    for b in blocked:
        if b in text.lower():
            return False, b
    return True, None


# ---------------- ANSIBLE ----------------
def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout if result.stdout else result.stderr


def validate(path):
    return run(["ansible-playbook", path, "--syntax-check"])


def dry_run(path):
    return run(["ansible-playbook", path, "--check"])


def execute(path):
    return run(["ansible-playbook", path])


# ---------------- LLM ----------------
def generate(user_input, history, error=None):
    prompt = f"""
You are an expert Ansible automation engine.

STRICT:
- Output ONLY YAML
- No markdown
- Must start with '- name:'

RULES:
- Valid modules only
- Correct parameters

IMPORTANT:
- user module does NOT support sudo
- use groups: sudo

Conversation:
{history}

User:
{user_input}
"""

    if error:
        prompt += f"\n\nFix this error:\n{error}"

    return llm.invoke(prompt).strip()


# ---------------- AUTO REPAIR LOOP ----------------
def generate_with_repair(user_input, history):
    error = None

    for _ in range(3):
        raw = generate(user_input, history, error)
        cleaned = clean_yaml_output(raw)

        parsed, err = parse_yaml(cleaned)
        if err:
            error = err
            continue

        parsed = sanitize_playbook(parsed, user_input)

        final_yaml = yaml.dump(parsed, sort_keys=False)

        safe, pattern = is_safe(final_yaml)
        if not safe:
            return None, f"Blocked unsafe command: {pattern}"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".yml", mode="w") as f:
            f.write(final_yaml)
            path = f.name

        validation = validate(path)

        if "ERROR" in validation:
            error = validation
            continue

        return final_yaml, path

    return None, f"Failed after retries:\n{error}"


# ---------------- MAIN AGENT ----------------
def agent(user_input):
    global chat_history

    chat_history.append(f"User: {user_input}")
    history = "\n".join(chat_history[-5:])

    yaml_text, result = generate_with_repair(user_input, history)

    if not yaml_text:
        return result

    path = result

    dry = dry_run(path)
    run_out = execute(path)

    return (
        "===== FINAL YAML =====\n"
        + yaml_text
        + "\n\n===== DRY RUN =====\n"
        + dry
        + "\n\n===== EXECUTION =====\n"
        + run_out
    )
