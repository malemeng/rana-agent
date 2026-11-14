import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../features/activity/presentation/activity_page.dart';
import '../features/approvals/presentation/approvals_page.dart';
import '../features/chat/presentation/chat_page.dart';
import '../features/goals/presentation/goals_tasks_page.dart';
import '../features/memories/presentation/memories_page.dart';
import '../features/schedule/presentation/schedule_page.dart';
import '../features/settings/presentation/settings_page.dart';
import 'providers.dart';

class MainShell extends ConsumerStatefulWidget {
  const MainShell({super.key});

  @override
  ConsumerState<MainShell> createState() => _MainShellState();
}

class _MainShellState extends ConsumerState<MainShell> {
  int _index = 0;

  static const _pages = <Widget>[
    ChatPage(),
    GoalsTasksPage(),
    ApprovalsPage(),
    ActivityPage(),
    MemoriesPage(),
    SchedulePage(),
    SettingsPage(),
  ];

  static const _destinations = <NavigationDestination>[
    NavigationDestination(icon: Icon(Icons.chat_bubble_outline), label: '对话'),
    NavigationDestination(icon: Icon(Icons.task_alt_outlined), label: '目标任务'),
    NavigationDestination(icon: Icon(Icons.approval_outlined), label: '审批'),
    NavigationDestination(icon: Icon(Icons.bolt_outlined), label: '活动'),
    NavigationDestination(icon: Icon(Icons.psychology_outlined), label: '记忆'),
    NavigationDestination(
      icon: Icon(Icons.calendar_month_outlined),
      label: '日程',
    ),
    NavigationDestination(icon: Icon(Icons.settings_outlined), label: '设置'),
  ];

  @override
  Widget build(BuildContext context) {
    ref.watch(realtimeControllerProvider);
    return LayoutBuilder(
      builder: (context, constraints) {
        final useRail = constraints.maxWidth >= 800;
        final content = IndexedStack(index: _index, children: _pages);
        if (useRail) {
          return Scaffold(
            body: SafeArea(
              child: Row(
                children: <Widget>[
                  NavigationRail(
                    selectedIndex: _index,
                    extended: constraints.maxWidth >= 1280,
                    labelType: constraints.maxWidth >= 1280
                        ? NavigationRailLabelType.none
                        : NavigationRailLabelType.all,
                    onDestinationSelected: (value) =>
                        setState(() => _index = value),
                    destinations: _destinations
                        .map(
                          (item) => NavigationRailDestination(
                            icon: item.icon,
                            selectedIcon: item.selectedIcon,
                            label: Text(item.label),
                          ),
                        )
                        .toList(growable: false),
                  ),
                  const VerticalDivider(width: 1),
                  Expanded(child: content),
                ],
              ),
            ),
          );
        }
        return Scaffold(
          body: content,
          bottomNavigationBar: NavigationBar(
            selectedIndex: _index,
            labelBehavior: NavigationDestinationLabelBehavior.onlyShowSelected,
            onDestinationSelected: (value) => setState(() => _index = value),
            destinations: _destinations,
          ),
        );
      },
    );
  }
}
