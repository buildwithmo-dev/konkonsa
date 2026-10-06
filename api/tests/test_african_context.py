from services.african_context import analyze


def test_ghana_signals_combine():
    r = analyze("Paid 500 GHS for a trotro ride again, dumsor is killing us")
    assert r.is_relevant and r.primary_country == "GH"


def test_single_city_is_enough():
    assert analyze("Lagos traffic is wild today").primary_country == "NG"


def test_subreddit_hint_alone_is_relevant():
    r = analyze("Anyone know a good plumber?", hint="r/Kenya")
    assert r.is_relevant and r.primary_country == "KE"


def test_generic_post_is_not_relevant():
    assert not analyze("Best laptop for programming in 2026?").is_relevant


def test_african_american_is_not_africa():
    assert not analyze("African American history month events").is_relevant


def test_mad_word_is_not_moroccan_dirham():
    assert not analyze("I'm so mad about this bug").is_relevant


def test_south_africa_is_not_double_counted_as_pan_african():
    r = analyze("Eskom announced more load shedding in South Africa")
    assert r.primary_country == "ZA" and "pan_african" not in r.signals