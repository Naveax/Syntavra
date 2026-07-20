# SignalBench external-arm protocol

SignalBench runs each product as an independent external adapter. Competitor source code is never imported into SignalCore. A valid comparison freezes the repository tree, prompt, model, reasoning mode, context window, verifier, permissions, timeout, cache policy and hardware class.

Adapters receive a JSON request through `{request}`, operate only in `{workspace}`, and write provider-reported usage plus verifier metadata to `{output}`. Failed tasks receive zero verified work. Public superiority requires at least ten valid paired repetitions and a 95% bootstrap confidence-interval lower bound above 1.0; a 5X claim requires the configured claim-governance gate to pass at 5.0.

The example task and arm files are templates, not evidence. Live credentials, frozen repositories and exact competitor versions must be supplied by the benchmark operator.
