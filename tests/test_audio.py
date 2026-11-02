import struct
import unittest

from app.audio import estimate_noise_floor, normalize_speech_pcm, pcm_frame_rms, speech_threshold


class AudioHelpersTests(unittest.TestCase):
    def test_pcm_frame_rms_uses_signed_little_endian_samples(self):
        frame = struct.pack("<320h", *([1000] * 320))
        self.assertAlmostEqual(pcm_frame_rms(frame), 1000.0)

    def test_pcm_frame_rms_rejects_incomplete_frames(self):
        self.assertEqual(pcm_frame_rms(b"\x00" * 20), 0.0)

    def test_noise_floor_uses_quieter_half_to_reject_transient_peaks(self):
        self.assertAlmostEqual(estimate_noise_floor([40, 42, 39, 41, 1500, 1800]), 40.5)

    def test_speech_threshold_has_floor_and_tracks_noise(self):
        self.assertEqual(speech_threshold(25), 95.0)
        self.assertAlmostEqual(speech_threshold(65), 117.0)

    def test_normalization_lifts_quiet_speech_without_clipping(self):
        pcm = struct.pack("<640h", *([500] * 640))
        normalized, gain, original_rms = normalize_speech_pcm(pcm)
        output = struct.unpack("<640h", normalized)
        self.assertGreater(gain, 1.0)
        self.assertLessEqual(max(abs(value) for value in output), 30000)
        self.assertGreater(original_rms, 0.0)
        self.assertGreater(sum(value * value for value in output), sum(value * value for value in struct.unpack("<640h", pcm)))

    def test_normalization_leaves_loud_audio_unchanged(self):
        pcm = struct.pack("<640h", *([10000] * 640))
        normalized, gain, _ = normalize_speech_pcm(pcm)
        self.assertEqual(normalized, pcm)
        self.assertEqual(gain, 1.0)

    def test_normalization_leaves_silence_unchanged(self):
        pcm = b"\x00" * 1280
        normalized, gain, speech_rms = normalize_speech_pcm(pcm)
        self.assertEqual(normalized, pcm)
        self.assertEqual(gain, 1.0)
        self.assertEqual(speech_rms, 0.0)


if __name__ == "__main__":
    unittest.main()
