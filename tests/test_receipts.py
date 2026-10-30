from clipper import receipts

DATA = {"subject": "jer", "show": "Kino Casino",
        "stories": [{"story": "The Lawsuit", "use": True}, {"story": "Skipped", "use": False},
                    {"story": "The Merch", "use": True}]}


def test_the_default_title_is_the_old_one(defaults):
    title, _ = receipts.default_text(DATA, defaults)
    assert title == "The Quartering Receipts: The Lawsuit, The Merch"


def test_another_subject_without_a_title_is_named_after_itself(defaults):
    defaults["receipts"]["title"] = ""
    title, _ = receipts.default_text({**DATA, "subject": "ethan"}, defaults)
    assert title == "ethan receipts: The Lawsuit, The Merch"
