const vals = [
  0, -0, 1, 1.0, -1, 0.5, 1e16, 1e17, 1e20, 1e21, 1e22, 1e23,
  1e-6, 1e-7, 1e-5, 5e-324, 1.7976931348623157e308,
  9007199254740992, 333333333.3333333,
  1424953923781206.2, 0.1, 2/3, 100, 1000000,
  1.5e300, -1.5e-300, 2**53, 2**63, 1e-323, 1e-322,
  123456789012345678901234567890, 1e-4, 0.000001, 0.0000001,
  1234567890123456789012, 4.5e-100, 1e300, 2.220446049250313e-16,
  0.30000000000000004, 1e-11, 12345678901234567890, 3.0, -0.0
];
for (const v of vals) {
  console.log(JSON.stringify(String(v)) + "  |  " + JSON.stringify(v));
}
console.log("---appendix A sort---");
const keys = ["\u20ac", "\u000d", "\ufb33", "\u0031", "\ud83d\ude00", "\u0080", "\u00f6"];
console.log(JSON.stringify(JSON.stringify(keys.slice().sort())));
console.log("---astral vs bmp sort---");
const k2 = ["\ud83d\ude00", "\ufb33", "\ue000", "\uffff", "\u007a", "\ud800\udc00", "\udbff\udfff", "\u0080"];
console.log(JSON.stringify(JSON.stringify(k2.slice().sort())));
console.log("---stringify of appendix A object---");
const o = {}; for (const k of keys) o[k] = 1;
console.log(JSON.stringify(JSON.stringify(o)));
console.log("---escaping probe---");
const p = "\u0008\u0009\u000a\u000b\u000c\u000d\u001f\u0020\u0022\u002f\u005c\u007f\u00e9\u20ac\ud83d\ude00";
console.log(JSON.stringify(JSON.stringify(p)));
