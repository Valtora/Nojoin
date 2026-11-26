---
tags:
  - pyannote
  - pyannote-audio
  - pyannote-audio-model
  - wespeaker
  - audio
  - voice
  - speech
  - speaker
  - speaker-recognition
  - speaker-verification
  - speaker-identification
  - speaker-embedding
datasets:
  - voxceleb
license: cc-by-4.0
inference: false
---

Using this open-source model in production?  
Consider switching to [pyannoteAI](https://www.pyannote.ai) for better and faster options.

# 🎹 Wrapper around wespeaker-voxceleb-resnet34-LM

This model requires `pyannote.audio` version 3.1 or higher.

This is a wrapper around [WeSpeaker](https://github.com/wenet-e2e/wespeaker) `wespeaker-voxceleb-resnet34-LM` pretrained speaker embedding model, for use in `pyannote.audio`.

## Basic usage

```python
# instantiate pretrained model
from pyannote.audio import Model
model = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")
```

```python
from pyannote.audio import Inference
inference = Inference(model, window="whole")
embedding1 = inference("speaker1.wav")
embedding2 = inference("speaker2.wav")
# `embeddingX` is (1 x D) numpy array extracted from the file as a whole.

from scipy.spatial.distance import cdist
distance = cdist(embedding1, embedding2, metric="cosine")[0,0]
# `distance` is a `float` describing how dissimilar speakers 1 and 2 are.
```

## Advanced usage

### Running on GPU

```python
import torch
inference.to(torch.device("cuda"))
embedding = inference("audio.wav")
```

### Extract embedding from an excerpt

```python
from pyannote.audio import Inference
from pyannote.core import Segment
inference = Inference(model, window="whole")
excerpt = Segment(13.37, 19.81)
embedding = inference.crop("audio.wav", excerpt)
# `embedding` is (1 x D) numpy array extracted from the file excerpt.
```

### Extract embeddings using a sliding window

```python
from pyannote.audio import Inference
inference = Inference(model, window="sliding",
                      duration=3.0, step=1.0)
embeddings = inference("audio.wav")
# `embeddings` is a (N x D) pyannote.core.SlidingWindowFeature
# `embeddings[i]` is the embedding of the ith position of the
# sliding window, i.e. from [i * step, i * step + duration].
```

## License

According to [this page](https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md):

> The pretrained model in WeNet follows the license of it's corresponding dataset. For example, the pretrained model on VoxCeleb follows Creative Commons Attribution 4.0 International License., since it is used as license of the VoxCeleb dataset, see https://mm.kaist.ac.kr/datasets/voxceleb/.

## Citation

```bibtex
@inproceedings{Wang2023,
  title={Wespeaker: A research and production oriented speaker embedding learning toolkit},
  author={Wang, Hongji and Liang, Chengdong and Wang, Shuai and Chen, Zhengyang and Zhang, Binbin and Xiang, Xu and Deng, Yanlei and Qian, Yanmin},
  booktitle={ICASSP 2023, IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)},
  pages={1--5},
  year={2023},
  organization={IEEE}
}
```

```bibtex
@inproceedings{Bredin23,
  author={Hervé Bredin},
  title={{pyannote.audio 2.1 speaker diarization pipeline: principle, benchmark, and recipe}},
  year=2023,
  booktitle={Proc. INTERSPEECH 2023},
  pages={1983--1987},
  doi={10.21437/Interspeech.2023-105}
}
```
