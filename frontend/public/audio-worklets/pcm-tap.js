/*
 * Nojoin capture: forwards the mixer's audio to the main thread unchanged.
 *
 * Runs on the audio rendering thread. It copies every render quantum into
 * fixed-size blocks, transfers each full block to the page, and passes the
 * audio through to its output so the analysers downstream keep working. It
 * holds no logic beyond that: resampling, segmenting and FLAC encoding happen
 * in frontend/src/lib/capture/pcm/, where they are typed and tested.
 *
 * Messages to the page:   { type: "pcm", channels: Float32Array[] }
 *                         { type: "flushed", id }
 * Messages from the page: { type: "flush", id }  send the partial block, then ack
 *                         { type: "stop" }       flush and stop processing
 */
class PcmTapProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const processorOptions = (options && options.processorOptions) || {};
    this.blockFrames = processorOptions.blockFrames || 4096;
    this.channelCount = processorOptions.channelCount || 2;
    this.active = true;
    this.allocate();
    this.port.onmessage = (event) => {
      const message = event.data || {};
      if (message.type === "flush") {
        this.flush();
        this.port.postMessage({ type: "flushed", id: message.id });
      } else if (message.type === "stop") {
        this.flush();
        this.active = false;
      }
    };
  }

  allocate() {
    this.blocks = [];
    for (let channel = 0; channel < this.channelCount; channel += 1) {
      this.blocks.push(new Float32Array(this.blockFrames));
    }
    this.filled = 0;
  }

  flush() {
    if (this.filled === 0) {
      return;
    }
    const channels = this.blocks.map((block) => block.slice(0, this.filled));
    this.port.postMessage(
      { type: "pcm", channels },
      channels.map((channel) => channel.buffer),
    );
    this.allocate();
  }

  process(inputs, outputs) {
    const input = inputs[0] || [];
    const output = outputs[0] || [];
    const frames = (output[0] && output[0].length) || (input[0] && input[0].length) || 128;
    let written = 0;
    while (written < frames) {
      const take = Math.min(frames - written, this.blockFrames - this.filled);
      for (let channel = 0; channel < this.channelCount; channel += 1) {
        // An input with no active source arrives with no channels: silence.
        const source = input[channel];
        if (source) {
          this.blocks[channel].set(source.subarray(written, written + take), this.filled);
        }
      }
      this.filled += take;
      written += take;
      if (this.filled === this.blockFrames) {
        this.flush();
      }
    }
    for (let channel = 0; channel < output.length; channel += 1) {
      const source = input[channel];
      if (source) {
        output[channel].set(source);
      }
    }
    return this.active;
  }
}

registerProcessor("nojoin-pcm-tap", PcmTapProcessor);
