import subprocess
from langchain_ollama import OllamaLLM

llm = OllamaLLM(model="llama3")

chat_history = []


def run_ansible(playbook):
    try:
        result = subprocess.run(
            ["ansible-playbook", f"playbooks/{playbook}"],
            capture_output=True,
            text=True
        )

        output = result.stdout if result.stdout else result.stderr

        return f"Running {playbook}...\n\n{output}"

    except Exception as e:
        return f"Error: {str(e)}"


def agent(user_input):
    global chat_history

    chat_history.append(f"User: {user_input}")
    history_context = "\n".join(chat_history[-6:])

    prompt = f"""
You are an AI DevOps assistant.

You have 2 modes:
1. Chat → normal conversation
2. Action → execute Ansible

Available playbooks:
- install_nginx.yml

STRICT RULES:
- If action needed → respond ONLY:
  RUN:<playbook.yml>
- DO NOT explain
- DO NOT add extra text
- DO NOT simulate output
- If chat → respond normally

Conversation:
{history_context}

Assistant:
"""

    try:
        response = llm.invoke(prompt).strip()
    except Exception as e:
        return f"LLM error: {str(e)}"

    chat_history.append(f"Assistant: {response}")

    if response.startswith("RUN:"):
        playbook = response.replace("RUN:", "").strip()
        return run_ansible(playbook)

    return response
