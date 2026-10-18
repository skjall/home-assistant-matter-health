from matter_health.batteries import batteries, battery, wanted


def test_which_attributes_tell_about_a_battery() -> None:
    assert wanted("1/47/11")
    assert wanted("0/47/27")
    assert not wanted("1/47/12")
    assert not wanted("1/6/0")
    assert not wanted("47/11")


def test_a_battery_by_its_cells() -> None:
    assert battery({"1/47/11": 3140, "1/47/27": 2}) == {
        "millivolts": 3140,
        "cells": 2,
        "cell_millivolts": 1570,
        "weak": False,
    }
    # One cell, as the voltage says; worn.
    assert battery({"1/47/11": 1243})["weak"] is True
    # Two cells or a coin cell: cannot tell per cell.
    assert battery({"0/47/11": 2950}) == {
        "millivolts": 2950,
        "cells": None,
        "cell_millivolts": None,
        "weak": False,
    }
    assert battery({"1/47/11": 0}) is None
    assert battery({"1/6/0": True}) is None


def test_batteries_by_subject() -> None:
    assert batteries({3: {"1/47/11": 1500}, 4: {"1/6/0": True}}) == {
        "node:3": {
            "millivolts": 1500,
            "cells": 1,
            "cell_millivolts": 1500,
            "weak": False,
        }
    }
