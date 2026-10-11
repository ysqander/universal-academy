# Example course: Linear Algebra Done Right

A ready-made Universal Academy course on chapter 1, *Vector Spaces*, of Sheldon Axler's *Linear Algebra Done Right*
(4th edition), so you can try the app before building a course of your own. Studying it needs no API key.

**Add it:** start the app (`./run.sh`), open the **Library**, and press **Add to my library** on the
**Example course** card. The app copies the book and the course into your library; this folder is not changed.

## What this folder holds

- `book.pdf`: the book's Open Access PDF (content not modified), used for page references and the source view.
- `course.json`: the course: knowledge graph, lessons, worked examples, practice and quiz questions, hints,
  solutions and visuals, plus the book's metadata. It holds no learner data (no progress, answers or build logs).
- `assets/`: the course's figures (cropped from the book's pages).

The bundle is written by `backend/scripts/export_course.py` and read by `backend/app/bundle.py`.

## License and attribution

Not covered by the repository's MIT license. Both the book and the course are licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/): **noncommercial use only.**

- Book: Sheldon Axler, *Linear Algebra Done Right*, 4th edition, Undergraduate Texts in Mathematics, Springer, 2024.
  © 2024 Sheldon Axler, Open Access. <https://linear.axler.net>
- Course: an adaptation of the book, generated from its pages by large language models with Universal Academy and
  partly edited by hand. Changes: the material is split into small skills with prerequisites, rewritten as lessons
  with new worked examples and visuals, and new questions, hints and solutions were written. It may contain mistakes
  the book does not have and is not reviewed or endorsed by Sheldon Axler or Springer.

Full notice: [NOTICE.md](../../NOTICE.md).
