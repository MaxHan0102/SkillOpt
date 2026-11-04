"""Read trajectory directories using the official task-ID path mapping."""
from __future__ import annotations

from functools import wraps
from pathlib import Path

from skillopt.envs.task_output import task_output_segment


def install_mapped_trajectory_reader() -> None:
    from skillopt.gradient import reflect

    original = reflect.fmt_minibatch_trajectories
    if getattr(original, "_uses_task_output_mapping", False):
        return

    @wraps(original)
    def mapped_reader(items: list[dict], prediction_dir: str) -> str:
        prepared = []
        replacements = {}
        for item in items:
            task_id = str(item["id"])
            segment = task_output_segment(task_id, map_unsafe=True)
            if segment != task_id:
                conversation = Path(prediction_dir) / segment / "conversation.json"
                if not conversation.is_file():
                    # ALFWorld still writes raw IDs such as train:0000 on Linux.
                    # Only use a single confined directory component as fallback.
                    root = Path(prediction_dir).resolve()
                    raw = root / task_id / "conversation.json"
                    if (Path(task_id).name == task_id and "\\" not in task_id
                            and raw.resolve().is_relative_to(root) and raw.is_file()):
                        prepared.append(item)
                        continue
                    raise FileNotFoundError(f"Trajectory missing for {task_id}")
                prepared.append({**item, "id": segment})
                replacements[segment] = task_id
            else:
                prepared.append(item)
        text = original(prepared, prediction_dir)
        # Keep the public IDs in analyst prompts; change only filesystem lookup.
        for segment, task_id in replacements.items():
            text = text.replace(f"(id={segment})", f"(id={task_id})")
        return text

    mapped_reader._uses_task_output_mapping = True
    reflect.fmt_minibatch_trajectories = mapped_reader
