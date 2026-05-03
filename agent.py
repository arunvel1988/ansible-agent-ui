import subprocess
import tempfile
import yaml
from langchain_ollama import OllamaLLM

# ---------------- LLM ----------------
llm = OllamaLLM(model="llama3")

chat_history = []


# ---------------- CLEAN OUTPUT ----------------
def clean_yaml(text):
    text = text.strip()

    if "```" in text:
        parts = text.split("```")
        for p in parts:
            if "- name:" in p:
                text = p.replace("yaml", "").strip()
                break

    if "- name:" in text:
        text = text[text.index("- name:"):]

    return text.strip()


# ---------------- PARSE YAML ----------------
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


# ---------------- ANSIBLE EXECUTION ----------------
def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout if result.stdout else result.stderr


def validate(path):
    return run(["ansible-playbook", path, "--syntax-check"])


def dry_run(path):
    return run(["ansible-playbook", path, "--check"])


def execute(path):
    return run(["ansible-playbook", path])


# ---------------- LLM PROMPT ----------------
def generate(user_input, history, error=None):
    prompt = f"""
You are an Ansible expert AI.

Decide request type:

1. PLAYBOOK GENERATION
- If user asks to perform a task → output ONLY YAML playbook

2. ANSIBLE KNOWLEDGE
- If user asks concept → output TEXT explanation

3. NON-ANSIBLE
- If unrelated → output EXACTLY:
INVALID REQUEST: ONLY ANSIBLE SUPPORTED

PLAYBOOK RULES:
- MUST start with '- name:'
- Use proper YAML indentation
- hosts: localhost
- become: true when needed
- Use valid Ansible modules ONLY
- NO markdown
- NO ``` blocks

USER MODULE:
- DO NOT use sudo param
- Use:
  groups: sudo
  append: true

COMMAND RULE:
- Use 'command'
- Add register + debug ONLY if output needed

Conversation:
{history}

User:
{user_input}
"""

    if error:
        prompt += f"\nFix this error:\n{error}"

    return llm.invoke(prompt).strip()


# ---------------- AUTO REPAIR ----------------
def generate_with_repair(user_input, history):
    error = None

    for _ in range(4):
        raw = generate(user_input, history, error)

        print("\n=== RAW LLM OUTPUT ===\n", raw)

        # Handle non-ansible text response
        if "INVALID REQUEST" in raw:
            return None, raw

        # If it's NOT YAML → treat as chat response
        if not raw.strip().startswith("- name:"):
            return None, raw

        cleaned = clean_yaml(raw)

        parsed, err = parse_yaml(cleaned)
        if err:
            error = err
            continue

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

    # TEXT response (chat or invalid)
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


# ---------------- CLI LOOP ----------------
if __name__ == "__main__":
    print("Ansible AI Agent Ready")
    while True:
        user_input = input(">> ")
        if user_input.lower() in ["exit", "quit"]:
            break
        print(agent(user_input))
