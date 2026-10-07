# Basic probability

For events A and B with P(B) > 0, conditional probability is defined by
`P(A | B) = P(A and B) / P(B)`.

Bayes' theorem relates two conditional probabilities:
`P(A | B) = P(B | A) * P(A) / P(B)`, provided P(B) > 0.

Two events are independent when `P(A and B) = P(A) * P(B)`. If P(B) > 0,
independence implies `P(A | B) = P(A)`. Mutually exclusive events with positive
probabilities are not independent: their intersection has probability zero.

## Bernoulli random variable

A Bernoulli random variable X takes the value 1 with probability p and the value
0 with probability 1 - p. Its expectation is `E[X] = p`, and its variance is
`Var(X) = p * (1 - p)`.

For p = 0.25, the expectation is 0.25 and the variance is 0.1875.

This original demonstration note is a small service fixture, not a full textbook.
