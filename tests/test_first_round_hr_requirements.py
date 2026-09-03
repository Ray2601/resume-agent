"""首轮简历撰写应提前获得 HR 的验收标准。"""

from config.prompts.hr_prompt import HR_WRITING_REQUIREMENTS


def test_hr_writing_requirements_cover_all_scoring_dimensions():
    for dimension in ("匹配度", "数据化", "影响力", "简洁度"):
        assert dimension in HR_WRITING_REQUIREMENTS
    assert "93分以上" in HR_WRITING_REQUIREMENTS
    assert "不得编造" in HR_WRITING_REQUIREMENTS


def test_first_round_crewai_task_contains_hr_requirements(monkeypatch):
    import src.crewai.tasks as tasks

    class FakeTask:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(tasks, "Task", FakeTask)
    task = tasks.create_synthesis_task(
        agent=object(),
        jd="目标JD",
        phase1_summary="原始经历摘要",
        optimization_focus="首轮生成",
    )

    assert HR_WRITING_REQUIREMENTS in task.description
    assert task.description.index("原始经历摘要") < task.description.index("HR验收标准")
