from app.agents.analytics.grounding import find_unverified_numbers

ROWS = [{"region": "West", "rev": 1234567.891, "share": 61.73}, {"region": "East", "rev": 765432.1, "share": 38.27}]


def test_matching_numbers_pass():
    text = "West earned 1,234,567.89 (61.7%) versus East's 765,432."
    assert find_unverified_numbers(text, ROWS) == []


def test_abbreviated_numbers_pass():
    assert find_unverified_numbers("West brought in $1.23M.", ROWS) == []


def test_invented_number_flagged():
    assert find_unverified_numbers("West earned 9,999 and grew 12%.", ROWS) == ["9,999", "12%"]


def test_small_counting_words_ignored():
    assert find_unverified_numbers("The top 3 regions are listed.", ROWS) == []


def test_numbers_inside_labels_count():
    rows = [{"date": "2024-01", "rev": 5.0}]
    assert find_unverified_numbers("In 2024-01 revenue was 5.", rows) == []
