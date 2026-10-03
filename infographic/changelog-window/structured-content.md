# Original scope, every page

Changelog collection keeps the original query scope across every page.
The pagination cursor may advance.
If the original request omits startTime, a next link must not add it.
If the original request supplies startTime, next links must preserve its value.
Changed scope fails before the next request and before encryption.
No database writes or outreach.

Visual: original request capsule, cursor slider and fixed time-window compartment; rejected extra startTime exits to stop before encryption.
