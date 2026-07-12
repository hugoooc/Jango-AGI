import json

from app_mapper.artifacts import create_observation_directory, write_json


def test_create_observation_directory_and_write_json(tmp_path) -> None:
    destination = create_observation_directory(tmp_path)
    output = destination / "sample.json"

    write_json(output, {"read_only": True})

    assert destination.parent == tmp_path
    assert json.loads(output.read_text()) == {"read_only": True}
