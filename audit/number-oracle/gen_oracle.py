"""Generate an ASCII-only oracle.js from code points (no literal chars in source)."""

NUMS = """
  0, -0, 1, 1.0, -1, 0.5, 1e16, 1e17, 1e20, 1e21, 1e22, 1e23,
  1e-6, 1e-7, 1e-5, 5e-324, 1.7976931348623157e308,
  9007199254740992, 333333333.3333333,
  1424953923781206.2, 0.1, 2/3, 100, 1000000,
  1.5e300, -1.5e-300, 2**53, 2**63, 1e-323, 1e-322,
  123456789012345678901234567890, 1e-4, 0.000001, 0.0000001,
  1234567890123456789012, 4.5e-100, 1e300, 2.220446049250313e-16,
  0.30000000000000004, 1e-11, 12345678901234567890, 3.0, -0.0
"""


def esc(cp):
    """One code point -> a JS string-literal escape (surrogate pair if astral)."""
    if cp < 0x10000:
        return "\\u%04x" % cp
    v = cp - 0x10000
    return "\\u%04x\\u%04x" % (0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF))


def lit(cps):
    return '"' + "".join(esc(c) for c in cps) + '"'


# RFC 8785 Appendix A sorting sample keys.
APPENDIX_A = [[0x20AC], [0x000D], [0xFB33], [0x31], [0x1F600], [0x80], [0xF6]]
# Astral vs BMP discriminators.
ASTRAL = [[0x1F600], [0xFB33], [0xE000], [0xFFFF], [0x7A],
          [0x10000], [0x10FFFF], [0x80]]

js = []
js.append("const vals = [%s];" % NUMS)
js.append("for (const v of vals) {")
js.append('  console.log(JSON.stringify(String(v)) + "  |  " + JSON.stringify(v));')
js.append("}")
js.append('console.log("---appendix A sort---");')
js.append("const keys = [%s];" % ", ".join(lit(k) for k in APPENDIX_A))
js.append("console.log(JSON.stringify(JSON.stringify(keys.slice().sort())));")
js.append('console.log("---astral vs bmp sort---");')
js.append("const k2 = [%s];" % ", ".join(lit(k) for k in ASTRAL))
js.append("console.log(JSON.stringify(JSON.stringify(k2.slice().sort())));")
js.append('console.log("---stringify of appendix A object---");')
js.append("const o = {}; for (const k of keys) o[k] = 1;")
js.append("console.log(JSON.stringify(JSON.stringify(o)));")
js.append('console.log("---escaping probe---");')
probe = [0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1F, 0x20, 0x22, 0x2F,
         0x5C, 0x7F, 0xE9, 0x20AC, 0x1F600]
js.append("const p = %s;" % lit(probe))
js.append("console.log(JSON.stringify(JSON.stringify(p)));")

out = "\n".join(js) + "\n"
path = ("/tmp/claude-1000/-home-travis-ProjectAmp2/"
        "aaa796b3-30cd-42f4-9fa0-2d43710658b7/scratchpad/oracle.js")
open(path, "w", encoding="ascii").write(out)
print("written", len(out))
