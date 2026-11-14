import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/providers.dart';
import 'app/shell.dart';
import 'features/auth/application/auth_controller.dart';
import 'features/auth/presentation/login_page.dart';

class RanaAgentApp extends ConsumerWidget {
  const RanaAgentApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final themeMode = ref.watch(
      settingsControllerProvider.select((state) => state.themeMode),
    );
    final lightScheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF6750A4),
    );
    final darkScheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF6750A4),
      brightness: Brightness.dark,
    );
    return MaterialApp(
      title: 'Rana Agent',
      debugShowCheckedModeBanner: false,
      locale: const Locale('zh', 'CN'),
      supportedLocales: const <Locale>[Locale('zh', 'CN'), Locale('en')],
      localizationsDelegates: const <LocalizationsDelegate<dynamic>>[
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      theme: _theme(lightScheme),
      darkTheme: _theme(darkScheme),
      themeMode: themeMode,
      home: switch (auth.status) {
        AuthStatus.restoring => const Scaffold(
          body: Center(child: CircularProgressIndicator()),
        ),
        AuthStatus.authenticated => const MainShell(),
        AuthStatus.unauthenticated ||
        AuthStatus.authenticating => const LoginPage(),
      },
    );
  }

  ThemeData _theme(ColorScheme scheme) => ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: scheme.surface,
    appBarTheme: AppBarTheme(
      centerTitle: false,
      backgroundColor: scheme.surface,
      surfaceTintColor: Colors.transparent,
    ),
    inputDecorationTheme: const InputDecorationTheme(
      border: OutlineInputBorder(),
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
  );
}
