import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../config/app_config.dart';
import '../../../shared/widgets/common.dart';
import '../application/auth_controller.dart';

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _baseUrlController;
  late final TextEditingController _usernameController;
  final _passwordController = TextEditingController();
  bool _obscurePassword = true;

  @override
  void initState() {
    super.initState();
    final store = ref.read(localStoreProvider);
    _baseUrlController = TextEditingController(text: store.baseUrl);
    _usernameController = TextEditingController(text: store.username);
  }

  @override
  void dispose() {
    _baseUrlController.dispose();
    _usernameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_formKey.currentState?.validate() != true) return;
    FocusScope.of(context).unfocus();
    await ref
        .read(authControllerProvider.notifier)
        .login(
          baseUrl: _baseUrlController.text,
          username: _usernameController.text.trim(),
          password: _passwordController.text,
        );
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authControllerProvider);
    final loading = auth.status == AuthStatus.authenticating;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ResponsiveContent(
              maxWidth: 480,
              child: Card(
                elevation: 1,
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: AutofillGroup(
                    child: Form(
                      key: _formKey,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: <Widget>[
                          Icon(
                            Icons.hub_outlined,
                            size: 56,
                            color: Theme.of(context).colorScheme.primary,
                          ),
                          const SizedBox(height: 16),
                          Text(
                            '服务器配对 / 登录',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.headlineSmall,
                          ),
                          const SizedBox(height: 8),
                          Text(
                            '连接你的 Personal Agent 服务',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          const SizedBox(height: 28),
                          TextFormField(
                            key: const Key('baseUrlField'),
                            controller: _baseUrlController,
                            enabled: !loading,
                            keyboardType: TextInputType.url,
                            decoration: const InputDecoration(
                              labelText: '后端 Base URL',
                              hintText: AppConfig.defaultBaseUrl,
                              prefixIcon: Icon(Icons.dns_outlined),
                              helperText: 'REST 根路径应包含 /api/v1',
                            ),
                            validator: _validateBaseUrl,
                          ),
                          const SizedBox(height: 16),
                          TextFormField(
                            key: const Key('usernameField'),
                            controller: _usernameController,
                            enabled: !loading,
                            autofillHints: const <String>[
                              AutofillHints.username,
                            ],
                            decoration: const InputDecoration(
                              labelText: '用户名',
                              prefixIcon: Icon(Icons.person_outline),
                            ),
                            validator: (value) =>
                                value == null || value.trim().isEmpty
                                ? '请输入用户名'
                                : null,
                          ),
                          const SizedBox(height: 16),
                          TextFormField(
                            key: const Key('passwordField'),
                            controller: _passwordController,
                            enabled: !loading,
                            obscureText: _obscurePassword,
                            autofillHints: const <String>[
                              AutofillHints.password,
                            ],
                            decoration: InputDecoration(
                              labelText: '密码',
                              prefixIcon: const Icon(Icons.lock_outline),
                              suffixIcon: IconButton(
                                onPressed: () => setState(
                                  () => _obscurePassword = !_obscurePassword,
                                ),
                                icon: Icon(
                                  _obscurePassword
                                      ? Icons.visibility_outlined
                                      : Icons.visibility_off_outlined,
                                ),
                              ),
                            ),
                            validator: (value) =>
                                value == null || value.isEmpty ? '请输入密码' : null,
                            onFieldSubmitted: (_) => _submit(),
                          ),
                          if (auth.error != null) ...<Widget>[
                            const SizedBox(height: 16),
                            ErrorBanner(message: auth.error!),
                          ],
                          const SizedBox(height: 24),
                          FilledButton.icon(
                            key: const Key('loginButton'),
                            onPressed: loading ? null : _submit,
                            icon: loading
                                ? const SizedBox.square(
                                    dimension: 18,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                    ),
                                  )
                                : const Icon(Icons.login),
                            label: Text(loading ? '正在连接…' : '配对并登录'),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  String? _validateBaseUrl(String? value) {
    if (value == null || value.trim().isEmpty) return '请输入后端 Base URL';
    final uri = Uri.tryParse(AppConfig.normalizeBaseUrl(value));
    if (uri == null ||
        !<String>{'http', 'https'}.contains(uri.scheme) ||
        uri.host.isEmpty) {
      return '请输入有效的 http(s) 地址';
    }
    return null;
  }
}
