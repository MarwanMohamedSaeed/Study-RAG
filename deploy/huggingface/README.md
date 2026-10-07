---
title: StudyRAG
emoji: 📚
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 8501
pinned: false
license: mit
short_description: Chat with lecture slides and get verified practice quizzes
---

# 📚 StudyRAG: live demo

Chat with lecture slides (with page citations), generate verified practice quizzes in four question types,
get cheat sheets, Arabic explanations, flashcards with spaced repetition, and timed exam simulations.

**This demo** runs on free resources:

- the app, the multilingual embeddings and the re-ranker run on this Space's free CPU;
- the LLM is called through **Groq's free API**, a quota shared by every visitor, so each session gets a
  limited number of AI actions;
- only the bundled sample lectures (computer networks: transport layer, network layer, routing slides) are
  available. Your quiz history and flashcards stay private to your browser session and are not kept.

To use **your own lectures** with no limits, run StudyRAG locally for free with Ollama: see the GitHub repository.
