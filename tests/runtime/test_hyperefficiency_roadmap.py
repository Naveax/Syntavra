from tools.validate_hyperefficiency_roadmap import validate

def test_hyperefficiency_roadmap_registry_is_contiguous_and_unique() -> None:
    assert validate() == []
