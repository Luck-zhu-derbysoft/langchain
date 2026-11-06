from app.infrastructure.skill.registry import SkillDescriptor, SkillRegistry


async def _echo(**kwargs: object) -> dict[str, object]:
    return dict(kwargs)


def _descriptor(name: str, *, enabled: bool = True) -> SkillDescriptor:
    return SkillDescriptor(name=name, func=_echo, metadata={"desc": name}, enabled=enabled)


def test_registered_skills_are_exposed_via_metadata_and_map() -> None:
    registry = SkillRegistry()
    registry.register(_descriptor("get_time"))

    assert registry.metadata() == [{"name": "get_time", "metadata": {"desc": "get_time"}}]
    assert registry.skills_map() == {"get_time": _echo}


def test_disabled_skill_is_hidden() -> None:
    registry = SkillRegistry()
    registry.register(_descriptor("on"))
    registry.register(_descriptor("off", enabled=False))

    assert [item["name"] for item in registry.metadata()] == ["on"]
    assert set(registry.skills_map()) == {"on"}


def test_duplicate_registration_overwrites_and_warns(caplog) -> None:  # type: ignore[no-untyped-def]
    registry = SkillRegistry()
    registry.register(_descriptor("dup"))

    with caplog.at_level("WARNING"):
        registry.register(SkillDescriptor(name="dup", func=_echo, metadata={"v": 2}))

    assert registry.metadata()[0]["metadata"] == {"v": 2}
    assert "already registered" in caplog.text
