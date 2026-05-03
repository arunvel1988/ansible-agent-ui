import subprocess
import tempfile
from langchain_ollama import OllamaLLM

llm = OllamaLLM(model="llama3")

chat_history = []


def clean_yaml_output(text):
    """
    Remove markdown fences and surrounding explanation text.
    Keep only raw YAML starting from first '- name:'.
    """
    text = text.strip()

    # Remove markdown blocks if present
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            if "- name:" in part:
                text = part.replace("yaml", "").strip()
                break

    # Keep only YAML starting from first '- name:'
    if "- name:" in text:
        text = text[text.index("- name:"):]

    return text.strip()


def fix_common_yaml_issues(yaml_text):
    """
    Fix common LLM indentation mistakes for Ansible structure.
    """
    lines = yaml_text.splitlines()
    fixed_lines = []

    inside_tasks = False

    for line in lines:
        stripped = line.strip()

        # top-level keys under play
        if stripped.startswith("hosts:") or stripped.startswith("become:"):
            fixed_lines.append("  " + stripped)
            continue

        if stripped.startswith("tasks:"):
            fixed_lines.append("  tasks:")
            inside_tasks = True
            continue

        # tasks list item
        if inside_tasks and stripped.startswith("- name:"):
            fixed_lines.append("    " + stripped)
            continue

        # task body
        if inside_tasks and stripped and not stripped.startswith("- name:"):
            # if already deeply indented, keep it
            if not line.startswith("      "):
                fixed_lines.append("      " + stripped)
            else:
                fixed_lines.append(line)
            continue

        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def is_safe(yaml_text):
    """
    Block obviously dangerous patterns.
    """
    blocked_patterns = [
        "rm -rf",
        "shutdown",
        "reboot",
        "mkfs",
        ":(){:|:&};:",
        "dd if=",
    ]

    lowered = yaml_text.lower()

    for pattern in blocked_patterns:
        if pattern in lowered:
            return False, pattern

    return True, None


def validate_playbook(file_path):
    """
    Syntax check generated playbook.
    """
    result = subprocess.run(
        ["ansible-playbook", file_path, "--syntax-check"],
        capture_output=True,
        text=True
    )

    return result.returncode == 0, result.stderr


def dry_run_playbook(file_path):
    """
    Run Ansible dry-run.
    """
    result = subprocess.run(
        ["ansible-playbook", file_path, "--check"],
        capture_output=True,
        text=True
    )

    return result.stdout if result.stdout else result.stderr


def execute_playbook(file_path):
    """
    Execute playbook for real.
    """
    result = subprocess.run(
        ["ansible-playbook", file_path],
        capture_output=True,
        text=True
    )

    return result.stdout if result.stdout else result.stderr


def generate_playbook(user_input, history_context):
    """
    Ask LLM to generate raw Ansible YAML only.
    """
    prompt = f"""
You are an expert Ansible automation engine.

STRICT OUTPUT RULES:
- Output ONLY raw YAML
- No markdown
- No ```yaml
- No explanations
- No headings
- Output must begin with: - name:

STRICT STRUCTURE:
- name: Play Name
  hosts: localhost
  become: yes

  tasks:
    - name: Task Name
      module_name:
        key: value

RULES:
- hosts must always be localhost
- always include become: yes
- tasks must be properly indented
- each task starts with '- name:'

MODULE RULES:
- apt for package install/remove on Ubuntu
- service for services
- file for files/directories
- user for users
- command or shell only if absolutely necessary

SAFETY:
- do not use rm -rf
- do not format disks
- do not reboot or shutdown

Conversation:
{history_context}

User request:
{user_input}
"""
    response = llm.invoke(prompt)
    return response.strip()


def agent(user_input):
    """
    Main entrypoint used by Flask app.
    """
    global chat_history

    chat_history.append(f"User: {user_input}")
    history_context = "\n".join(chat_history[-6:])

    try:
        yaml_content = generate_playbook(user_input, history_context)
    except Exception as e:
        return f"LLM error:\n{str(e)}"

    # cleanup and repair
    yaml_content = clean_yaml_output(yaml_content)
    yaml_content = fix_common_yaml_issues(yaml_content)

    # safety check
    safe, pattern = is_safe(yaml_content)
    if not safe:
        return f"Blocked unsafe command:\n{pattern}"

    # save temp playbook
    with tempfile.NamedTemporaryFile(delete=False, suffix=".yml", mode="w") as f:
        f.write(yaml_content)
        playbook_path = f.name

    # syntax validation
    valid, error = validate_playbook(playbook_path)
    if not valid:
        return (
            "Invalid playbook generated.\n\n"
            f"Validation error:\n{error}\n\n"
            f"Generated YAML:\n{yaml_content}"
        )

    # dry run
    dry_output = dry_run_playbook(playbook_path)

    # execute
    exec_output = execute_playbook(playbook_path)

    result = (
        "--- DRY RUN OUTPUT ---\n"
        f"{dry_output}\n\n"
        "--- EXECUTION OUTPUT ---\n"
        f"{exec_output}"
    )

    chat_history.append("Assistant: playbook executed")

    return result
