"""CrewAI step callbacks for visibility and debugging."""

from datetime import datetime
from src.utils.logger import get_logger

logger = get_logger("crewai_pipeline")


class StepLoggingCallback:
    """Logs every agent step: timestamp, agent name, task summary, output preview.

    Attach via Crew(step_callback=callback.on_step) or Task(callback=callback.on_step).
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.step_count = 0

    def on_step(self, step_output):
        self.step_count += 1
        agent_name = getattr(step_output, 'agent', 'Unknown')
        if hasattr(agent_name, 'role'):
            agent_name = agent_name.role

        output_str = str(getattr(step_output, 'raw', step_output))
        output_len = len(output_str)
        preview = output_str[:200].replace("\n", "\\n")

        timestamp = datetime.now().strftime("%H:%M:%S")

        if self.verbose:
            print(f"\n{'─' * 50}")
            print(f"  [Step {self.step_count}] {agent_name} — {timestamp}")
            print(f"  Output: {output_len} chars")
            print(f"  Preview: {preview}...")
            print(f"{'─' * 50}")

        logger.info(
            f"[Step {self.step_count}] agent={agent_name} "
            f"output_len={output_len} preview={preview}"
        )
