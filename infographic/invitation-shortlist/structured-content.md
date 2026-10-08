# Invitation shortlist
Learning objective: distinguish observed invitation history from read-only suggestions.
Input layers: All outgoing history. Exact people. Latest known employer.
Policy layer: Assumed present. Count each person once. Three per company.
Guard: Unknown identity or employer withholds recommendations.
Exclusions: Already invited. Connected. Opted out.
Priority: Activity. Mutuals. Connections. Profile photo.
Output: Up to25 invitations. Private report only. No Supabase writes. Owner sends manually.
