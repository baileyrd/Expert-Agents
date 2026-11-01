# remind-me "Protocol subset" variant: rejected (2026-11-01)

Variant: prompt at 055f36b. It adds one Break-it class: hand-rolled parsers are checked
against everything the governing spec lets a peer send. The class was written from the
baseline's only miss (346's HTTP framing keys), so a gain there would have been disclosed,
not blind. Raw run: d12a633, 25 runs, Eval-Kit, claude-opus-5-5. RUN.md has the provenance.

| Key | Baseline (judge) | Variant (judge) |
|---|---|---|
| 343-r1 K1, K2 | 5/5, 5/5 | 5/5, 5/5 |
| 343-r2 K1 | 5/5 | 5/5 |
| 344-r1 K1 | 5/5 | 5/5 |
| 346-r1 K1 | 5/5 | 5/5 |
| 346-r1 K2 chunked not decoded | 5/5 | 4/5 |
| 346-r2 K1 truncated after the last chunk | 0/5 | 0/5 |
| Total | 30/35 | 29/35 |

The target key is still 0/5. The new class did make the runs look at framing: all five
346-r2 reports check trailers and truncation. But each one then concludes that a truncated
body is refused. They cite the PR's own `a_truncated_chunked_response_is_an_error` test
rather than a probe of a body that ends at `0\r\n`. A category alone does not catch this
defect; catching it takes a probe of the exact terminator, and a prompt that asks for one
would name the answer key.

Decision: do not promote. The baseline prompt on main stays. These runs were not graded
for precision, because the variant is rejected on recall, its stated aim. The judge's
recall is the only score claimed here.
