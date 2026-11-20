"""Run the agent once: python -m agent.run "your task"."""

import sys

from agent.core import run_agent

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "Read the dev logs and tell me which service has errors."
    report = run_agent(task)
    print("\n--- answer ---")
    print(report.answer)
    print("\n--- tool calls ---")
    for c in report.tool_calls:
        print(f"  {c['name']}({c['arguments']})")
    print(f"\ntrace: http://127.0.0.1:16686/trace/{report.trace_id}")
