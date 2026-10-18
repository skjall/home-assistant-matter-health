from matter_health.reboots import rises


def test_a_rise_is_a_restart_and_a_first_count_only_a_start() -> None:
    counts, found = rises(
        {"node:1": 5, "node:2": 40},
        {
            1: {"0/51/1": 8, "0/51/4": 1},
            2: {"0/51/1": 3},  # reset or paired anew
            3: {"0/51/1": 700},  # new here
            4: {"0/51/1": True},
            5: {},
        },
    )

    assert counts == {"node:1": 8, "node:2": 3, "node:3": 700}
    assert found == [
        {"subject": "node:1", "count": 3, "total": 8, "reason": "power_on"}
    ]


def test_an_unknown_reason_is_left_out() -> None:
    _, found = rises({"node:1": 1}, {1: {"0/51/1": 2, "0/51/4": 99}})
    _, unsaid = rises({"node:1": 1}, {1: {"0/51/1": 2}})

    assert found[0]["reason"] is None
    assert unsaid[0]["reason"] is None
