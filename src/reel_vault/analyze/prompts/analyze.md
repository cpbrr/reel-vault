Turn this saved Instagram post into a note for my personal knowledge base.

I save reels so I can find them later when I need them: tips (job hunting, talking/communication, Claude and AI tooling), climbing videos, food recipes, games to play, desk/PC setups, memes, and random other things. I will search the notes by keyword, e.g. typing "job" before job hunting, so the note must surface what is useful and use the words I would search for.

## Existing categories

$categories

## Rules

- **category**: pick an existing category whenever it reasonably fits. Create a new one only when nothing fits; then give it a broad Title Case name ("Climbing", not "Bouldering Footwork") and a one-line `new_category_description`. Otherwise `new_category_description` is null.
- **related_categories**: up to 2 other existing categories this post also clearly belongs to, so it shows up on those pages too (e.g. a Claude skill for writing resumes: category "AI Tools", related "Career"). Only names from the list above, never the main category and never "Memes". Empty when it fits one category; most posts do.
- **is_meme**: true for jokes, memes and pure entertainment with nothing to learn or keep. Memes go in the "Memes" category, with a one-line summary of what happens and empty key_points. Memes still need full tags and keywords so a specific one can be found later: the joke or format, who or what is in it (people, characters, streamer, show, game, anime), memorable quotes or on-screen text, and the mood (e.g. "relatable", "gym humor").
- **title**: short, specific, describes the content (not clickbait from the caption). No emoji.
- **summary**: 2-4 sentences in English, whatever language the post is in.
- **key_points**: the concrete, reusable takeaways in English: tips, steps, techniques, ingredients and method, tool names, settings. Each point should make sense on its own when skimming. Empty for memes. For a climbing send, note the grade, board/angle and any beta.
- **tags**: 3-8 lowercase topic tags, single words or hyphenated (e.g. "interview", "claude-code", "kilter-board").
- **keywords**: 3-10 search phrases I might type that are NOT already obvious from the title, including synonyms and broader terms (e.g. a reel about "tell me about yourself" gets "job hunting", "interview prep", "career").
- **language**: ISO 639-1 code of the spoken/caption language.
- **repo_hints**: installable software only: GitHub repos, open-source tools, apps, Claude skills/plugins, MCP servers, CLIs. Especially when the link is gated behind "comment X and I'll DM you". Not brands, gear, gyms, games or accounts being tagged. For each, give the best search `query` (repo name, tool name, or creator + topic) and the `evidence` (what in the transcript, caption or image suggests it). Empty if none.
- **transcript_is_speech**: true if the transcript is someone talking about the post's content. False if it is song lyrics or background music, filler like "Thank you for watching", or unrelated noise. Only speech transcripts are kept in the note.

The transcript is automatic speech recognition and can contain errors. When it is not speech about the content, ignore it and rely on the caption.

## Post

- Author: @$owner
- Kind: $kind
- Posted: $posted
- URL: $url
- Hashtags: $hashtags

### Caption

$caption

### Transcript

$transcript
$images
