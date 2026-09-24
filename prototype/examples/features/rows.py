def tickets():  # what a dlt resource yields: dicts, typed values
    yield {"id": 1, "subject": "Charged twice", "body": "Two charges, mail me at a@b.com, key sk-abcdefghijklmnopqrstuvwx", "meta": {"n": 2}}
    yield {"id": 2, "subject": "App crashes", "body": "It crashes on login " + "x" * 50, "meta": None}
