from scopeguard.messages import (detect_format, load_messages, parse_file, parse_text_blob,
                                 parse_whatsapp)


def test_whatsapp_android(fix):
    msgs = parse_file(fix / "whatsapp_android.txt")
    assert [m.sender for m in msgs] == ["Sam Client", "Pat Freelancer", "Sam Client", "Sam Client"]
    assert msgs[0].text == "Hi! Can you add a gift card page?"
    assert msgs[0].ts == "2026-03-12T14:05:00"
    # continuation line joined, system line and <Media omitted> skipped
    assert msgs[2].text == "Second thing: please change the banner colour\nto a deeper green, and make the logo bigger."
    assert msgs[3].ts == "2026-03-13T21:02:00"
    assert "12:30 slot" in msgs[3].text      # a time inside the text does not start a new message


def test_whatsapp_ios(fix):
    msgs = parse_file(fix / "whatsapp_ios.txt")
    assert [m.sender for m in msgs] == ["Sam Client", "Pat Freelancer", "Sam Client", "Sam Client"]
    assert msgs[0].ts == "2026-03-12T14:05:33"        # 2:05:33 PM
    assert msgs[2].ts == "2026-03-13T09:30:12"
    assert msgs[2].text.endswith("make the logo bigger.")
    assert msgs[3].ts == "2026-03-13T21:02:44"        # 9:02:44 PM
    assert all("omitted" not in m.text for m in msgs)


def test_whatsapp_ios_invisible_characters():
    raw = ("‎[12/03/26, 2:05:33 PM] Sam Client: ‎Can you add a gift card page?\n"
           "‎[12/03/26, 2:06:10 PM] Pat: Sure.\n")
    msgs = parse_whatsapp(raw)
    assert len(msgs) == 2 and msgs[0].ts == "2026-03-12T14:05:33"
    assert msgs[0].text == "Can you add a gift card page?"


def test_whatsapp_us_date_order():
    raw = "3/13/26, 9:30 AM - Sam: Please add a page for the team\n3/14/26, 9:30 AM - Pat: ok\n"
    msgs = parse_whatsapp(raw)
    assert msgs[0].ts == "2026-03-13T09:30:00"


def test_eml_strips_quoted_reply(fix):
    msgs = parse_file(fix / "sample.eml")
    assert len(msgs) == 1
    assert msgs[0].sender == "Sam Client"
    assert "careers page" in msgs[0].text
    assert "pony" not in msgs[0].text and "Earlier quoted" not in msgs[0].text
    assert msgs[0].subject == "Extra pages"
    assert msgs[0].ts == "2026-03-12T14:05:00"


def test_mbox(fix):
    msgs = parse_file(fix / "sample.mbox")
    assert [m.sender for m in msgs] == ["Sam Client", "Pat Freelancer", "Sam Client"]
    assert msgs[2].text == "Please change the banner colour."


def test_plain_text(fix):
    msgs = parse_file(fix / "plain.txt")
    assert len(msgs) == 1 and msgs[0].sender == "client"
    assert "members area" in msgs[0].text


def test_slack_json_epoch(fix):
    msgs = parse_file(fix / "slack.json")
    assert [m.sender for m in msgs] == ["Sam Client", "Pat Freelancer", "Sam Client"]
    assert msgs[0].ts and msgs[0].ts.startswith("2026-03-12")


def test_upwork_json_nested(fix):
    msgs = parse_file(fix / "upwork.json")
    assert [m.sender for m in msgs] == ["Sam Client", "Pat Freelancer"]
    assert msgs[0].ts == "2026-03-12T14:05:00"


def test_slack_txt_and_chat_txt(fix):
    assert detect_format(fix / "slack.txt") == "slack"
    s = parse_file(fix / "slack.txt")
    assert s[0].sender == "Sam Client" and "responsive" in s[0].text and len(s) == 2
    assert detect_format(fix / "upwork.txt") == "chat"
    c = parse_file(fix / "upwork.txt")
    assert c[2].text == "Please change the banner colour\nand the font."


def test_detect_formats(fix):
    assert detect_format(fix / "whatsapp_android.txt") == "whatsapp"
    assert detect_format(fix / "whatsapp_ios.txt") == "whatsapp"
    assert detect_format(fix / "sample.eml") == "eml"
    assert detect_format(fix / "sample.mbox") == "mbox"
    assert detect_format(fix / "plain.txt") == "plain"


def test_load_messages_assigns_ids_and_folders(fix, tmp_path):
    (tmp_path / "a.txt").write_text((fix / "plain.txt").read_text())
    (tmp_path / "b.json").write_text((fix / "slack.json").read_text())
    msgs = load_messages([tmp_path])
    assert [m.id for m in msgs] == [f"M{i}" for i in range(1, len(msgs) + 1)]
    assert len(msgs) == 4


def test_parse_text_blob_eml_and_whatsapp(fix):
    assert parse_text_blob((fix / "sample.eml").read_text())[0].sender == "Sam Client"
    assert len(parse_text_blob((fix / "whatsapp_android.txt").read_text())) == 4


def test_tolerates_garbage(tmp_path):
    p = tmp_path / "junk.txt"
    p.write_bytes(b"\xff\xfe\x00 not really text \x00\x01")
    assert parse_file(p)  # plain fallback, no crash
    j = tmp_path / "bad.json"
    j.write_text("{not json")
    assert parse_file(j, "auto")  # falls back to plain text rather than raising
