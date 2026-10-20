"""Integer division must survive normalisation."""


def days(minutes):
    # the comment goes, the // stays
    return minutes // (60 * 24)


def halves(n):
    return n // 2  # also dropped
