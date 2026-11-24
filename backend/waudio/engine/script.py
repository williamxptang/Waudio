"""Chinese character conversion (OpenCC). Only touches text that contains CJK characters."""
CJK = range(0x3400, 0xA000)
SCRIPTS = {"as_is": "As the model writes them", "s2hk": "Traditional (Hong Kong)",
           "s2t": "Traditional", "s2tw": "Traditional (Taiwan)", "t2s": "Simplified"}
_converters = {}


def has_cjk(text):
    return any(ord(c) in CJK for c in text)


def convert(text, script):
    if script in (None, "", "as_is") or not has_cjk(text):
        return text
    if script not in SCRIPTS:
        raise ValueError(f"unknown script {script!r}")
    if script not in _converters:
        import opencc
        _converters[script] = opencc.OpenCC(script)
    return _converters[script].convert(text)
