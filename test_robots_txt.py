import api


def test_robots_txt_permits_all_paths():
    response = api.robots_txt()
    assert response == "User-agent: *\nAllow: /\n"
