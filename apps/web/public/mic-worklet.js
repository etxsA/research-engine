// Runs on the audio thread: hands the microphone samples to the page in blocks. The page turns
// them into a 16 kHz WAV (src/shared/lib/recorder.ts). A plain file from our own origin, so the
// Content-Security-Policy of the API stays at script-src 'self'.
class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(4096);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i += 1) {
      this.block[this.filled] = channel[i];
      this.filled += 1;
      if (this.filled === this.block.length) {
        this.port.postMessage(this.block);
        this.block = new Float32Array(4096);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic-capture", MicCapture);
