import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../shared/widgets/common.dart';

class SettingsPage extends ConsumerStatefulWidget {
  const SettingsPage({super.key});

  @override
  ConsumerState<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends ConsumerState<SettingsPage> {
  late final TextEditingController _baseUrl;

  @override
  void initState() {
    super.initState();
    _baseUrl = TextEditingController(
      text: ref.read(localStoreProvider).baseUrl,
    );
    Future.microtask(
      () => ref.read(settingsControllerProvider.notifier).refresh(),
    );
  }

  @override
  void dispose() {
    _baseUrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(settingsControllerProvider);
    final controller = ref.read(settingsControllerProvider.notifier);
    return AgentPageScaffold(
      title: '设置',
      actions: <Widget>[
        IconButton(
          onPressed: controller.refresh,
          icon: const Icon(Icons.refresh),
        ),
      ],
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          if (state.isLoading) const LinearProgressIndicator(minHeight: 2),
          if (state.error != null) ErrorBanner(message: state.error!),
          _Section(
            title: '服务器',
            children: <Widget>[
              TextField(
                controller: _baseUrl,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'Base URL',
                  helperText: '例如 http://10.0.2.2:8080/api/v1',
                ),
              ),
              const SizedBox(height: 12),
              OverflowBar(
                children: <Widget>[
                  OutlinedButton.icon(
                    onPressed: state.isTesting
                        ? null
                        : controller.testConnection,
                    icon: state.isTesting
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.network_check),
                    label: const Text('测试连接'),
                  ),
                  FilledButton.icon(
                    onPressed: () async {
                      await controller.saveBaseUrl(_baseUrl.text);
                      if (mounted) {
                        await ref
                            .read(realtimeControllerProvider.notifier)
                            .connect();
                      }
                    },
                    icon: const Icon(Icons.save_outlined),
                    label: const Text('保存地址'),
                  ),
                ],
              ),
              if (state.connectionResult != null)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(
                    state.connectionResult!.success
                        ? Icons.check_circle_outline
                        : Icons.error_outline,
                    color: state.connectionResult!.success
                        ? Colors.green
                        : Colors.red,
                  ),
                  title: Text(state.connectionResult!.message),
                ),
            ],
          ),
          _Section(
            title: '体验',
            children: <Widget>[
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('启用语音'),
                subtitle: const Text('麦克风采集与服务端 PCM 播报'),
                value: state.voiceEnabled,
                onChanged: controller.setVoiceEnabled,
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('通知'),
                subtitle: const Text('保留通知偏好设置'),
                value: state.notificationsEnabled,
                onChanged: controller.setNotificationsEnabled,
              ),
              const SizedBox(height: 8),
              Text('主题', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              SegmentedButton<ThemeMode>(
                segments: const <ButtonSegment<ThemeMode>>[
                  ButtonSegment(
                    value: ThemeMode.system,
                    label: Text('跟随系统'),
                    icon: Icon(Icons.brightness_auto_outlined),
                  ),
                  ButtonSegment(
                    value: ThemeMode.light,
                    label: Text('浅色'),
                    icon: Icon(Icons.light_mode_outlined),
                  ),
                  ButtonSegment(
                    value: ThemeMode.dark,
                    label: Text('深色'),
                    icon: Icon(Icons.dark_mode_outlined),
                  ),
                ],
                selected: <ThemeMode>{state.themeMode},
                onSelectionChanged: (values) =>
                    controller.setThemeMode(values.first),
              ),
            ],
          ),
          _Section(
            title: '服务端设置',
            children: state.remote.isEmpty
                ? const <Widget>[Text('暂未获取到服务端设置。')]
                : state.remote.entries
                      .map(
                        (entry) => ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(entry.key),
                          subtitle: Text('${entry.value}'),
                        ),
                      )
                      .toList(growable: false),
          ),
          Card(
            child: ListTile(
              leading: const Icon(Icons.logout),
              title: const Text('退出登录'),
              subtitle: Text(
                '当前账号：${ref.watch(authControllerProvider).username}',
              ),
              onTap: () => _confirmLogout(),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmLogout() async {
    final logout = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('退出登录'),
        content: const Text('将清除本机安全存储中的访问与刷新令牌。'),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('退出'),
          ),
        ],
      ),
    );
    if (logout == true) {
      await ref.read(authControllerProvider.notifier).logout();
    }
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Card(
    margin: const EdgeInsets.only(bottom: 16),
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    ),
  );
}
