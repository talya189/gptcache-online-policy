# Quora Question Pairs provenance notice

The final CARMA safety evaluation is derived from GPTCache's bundled
`examples/benchmark/similiar_qqp_full.json.gz` at baseline commit
`c59fb3a6152a4458b2a070ca183b61c4b614095f` (SHA-256
`1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58`).
The underlying human-labeled data is attributed to Quora's 2017
[Question Pairs release](https://quoradata.quora.com/First-Quora-Dataset-Release-Question-Pairs).

No question text, source archive, model weights, tokenizer files, or embedding
matrix is redistributed in this curated evidence directory. The original
archive in the GPTCache baseline has no accompanying local license file;
consult Quora's release terms before redistributing source records.

Embeddings were produced with the pinned Hugging Face repositories
[`GPTCache/paraphrase-albert-small-v2`](https://huggingface.co/GPTCache/paraphrase-albert-small-v2)
at revision `5fb246187b5489d59ce0db167e739192759defab` and
[`GPTCache/paraphrase-albert-onnx`](https://huggingface.co/GPTCache/paraphrase-albert-onnx)
at revision `5b562a100bc67e898ac89814e7a4668a18d65756`.
