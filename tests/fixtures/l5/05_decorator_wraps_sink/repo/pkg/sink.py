def audit(func):
    return func


@audit
def vulnerable():
    return "vulnerable"
