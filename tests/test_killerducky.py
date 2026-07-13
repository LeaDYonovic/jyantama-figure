from batchmortal.browser import (
    parse_killerducky_bad_move_stats,
    parse_killerducky_metadata,
)
from batchmortal.results import parse_metadata


def make_entry(actual_probability, *, is_equal=False, actual_index=1):
    details = [
        {"prob": 0.8},
        {"prob": actual_probability},
    ]
    return {
        "is_equal": is_equal,
        "actual_index": actual_index,
        "details": details,
    }


def test_parse_killerducky_about_metadata():
    data = {
        "engine": "Mortal",
        "game_length": "Hanchan",
        "review_time": "2s 709ms",
        "player_id": 2,
        "review": {
            "model_tag": "4.1b",
            "rating": 0.8422832528256039,
            "temperature": 0.1,
            "total_matches": 131,
            "total_reviewed": 173,
        },
    }

    metadata = parse_killerducky_metadata(data)

    assert metadata == {
        "engine": "Mortal",
        "model tag": "4.1b",
        "rating": "84.228",
        "matches/total": "131/173 = 75.723%",
        "temperature": "0.1",
        "game length": "Hanchan",
        "player id": "2",
        "review duration": "2s 709ms",
    }
    assert parse_metadata(metadata) == {
        "modelTag": "4.1b",
        "rating": "84.228",
        "aiConsistencyRate": "75.723%",
        "aiConsistencyNumerator": "131",
        "aiConsistencyDenominator": "173",
        "temperature": "0.1",
        "gameLength": "Hanchan",
        "playerId": "2",
        "reviewDuration": "2s 709ms",
    }


def test_parse_killerducky_bad_moves_uses_actual_choice_probability():
    invalid_entry = {
        "is_equal": False,
        "actual_index": 4,
        "details": [{"prob": 0.9}],
    }
    data = {
        "review": {
            "total_reviewed": 4,
            "kyokus": [
                {
                    "entries": [
                        make_entry(0.01, is_equal=True, actual_index=0),
                        make_entry(0.04),
                        make_entry(0.08),
                        invalid_entry,
                    ]
                }
            ],
        }
    }

    assert parse_killerducky_bad_move_stats(data) == {
        "badMoveRate5": "25.000%",
        "badMoveCount5": "1",
        "badMoveRate10": "50.000%",
        "badMoveCount10": "2",
        "badMoveDenominator": "4",
        "badMoveOrderLossCount": "3",
        "badMoveUnparsedCount": "1",
    }


def test_parse_killerducky_bad_moves_handles_missing_review_data():
    assert parse_killerducky_bad_move_stats({}) == {
        "badMoveRate5": "",
        "badMoveCount5": "0",
        "badMoveRate10": "",
        "badMoveCount10": "0",
        "badMoveDenominator": "",
        "badMoveOrderLossCount": "0",
        "badMoveUnparsedCount": "0",
    }
