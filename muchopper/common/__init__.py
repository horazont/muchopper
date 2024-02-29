import re

TAG_RE_STR = r"#(\w+)"
TAG_RE = re.compile(TAG_RE_STR)
TAGS_RE = re.compile(r"(" + TAG_RE_STR + r"\s*)+$")
