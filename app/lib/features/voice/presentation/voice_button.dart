import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../application/voice_controller.dart';

class VoiceButton extends ConsumerWidget {
  const VoiceButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final voice = ref.watch(voiceControllerProvider);
    final enabled = ref.watch(
      settingsControllerProvider.select((state) => state.voiceEnabled),
    );
    final busy = voice.phase == VoicePhase.connecting;
    final active = voice.phase == VoicePhase.listening;
    final speaking = voice.phase == VoicePhase.speaking;
    final label = switch (voice.phase) {
      VoicePhase.idle => '语音对话',
      VoicePhase.connecting => '正在连接语音…',
      VoicePhase.listening => '正在聆听，点击结束',
      VoicePhase.processing => '正在处理，点击取消',
      VoicePhase.speaking => '正在播报，点击打断',
      VoicePhase.error => voice.error ?? '语音错误，点击重试',
    };
    return Tooltip(
      message: enabled ? label : '请先在设置中启用语音',
      child: IconButton.filled(
        key: const Key('voiceButton'),
        onPressed: enabled && !busy
            ? () => ref.read(voiceControllerProvider.notifier).toggle()
            : null,
        icon: busy
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : Icon(
                !enabled
                    ? Icons.mic_off_outlined
                    : active
                    ? Icons.stop_outlined
                    : speaking
                    ? Icons.stop_circle_outlined
                    : Icons.mic_none_outlined,
              ),
      ),
    );
  }
}
