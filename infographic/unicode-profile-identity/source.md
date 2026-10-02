# Preserve Unicode profile identity
Provider URLs may contain Unicode names or UTF-8 percent-encoded aliases.
Reject control characters before parsing the URL.
Require a trusted LinkedIn host and one member profile path.
Decode valid UTF-8 once and preserve exact Unicode codepoints.
Reject malformed encoding, embedded separators, and controls.
Use the resulting identity for inbox evidence and invitation-history exclusions.
Do not transliterate names or merge different Unicode spellings.
