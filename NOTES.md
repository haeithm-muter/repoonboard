# Notes

After every milestone, answer both questions here before moving on.

## Milestone 1 (in progress)

**Weakest assumption so far:** that file-level import edges are enough to
recover a reading order. A repo built around dependency injection or a plugin
registry hides its real edges from static imports.

**What will break on a real repository:** TypeScript path aliases from
tsconfig, re-export barrels (`index.ts` that only re-exports), and Python
namespace packages. Resolution rate must be printed, not silently assumed.
