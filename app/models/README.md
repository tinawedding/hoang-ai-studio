# RNNoise model

The build downloads `std.rnnn`, the original Xiph RNNoise weights converted to
FFmpeg's arnndn text format, from the pinned revision of
https://github.com/richardpl/arnndn-models . No custom third-party training weights
are used. `scripts/fetch_model.py` checks the complete SHA-256 before accepting it.

RNNoise: https://github.com/xiph/rnnoise (BSD 3-Clause; see LICENSE-RNNOISE.txt).
This is speech noise suppression, not music stem separation or voice cloning.
