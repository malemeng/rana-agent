class ListState<T> {
  const ListState({
    this.items = const [],
    this.isLoading = false,
    this.error,
    this.busyIds = const <String>{},
  });

  final List<T> items;
  final bool isLoading;
  final String? error;
  final Set<String> busyIds;

  ListState<T> copyWith({
    List<T>? items,
    bool? isLoading,
    String? error,
    bool clearError = false,
    Set<String>? busyIds,
  }) {
    return ListState<T>(
      items: items ?? this.items,
      isLoading: isLoading ?? this.isLoading,
      error: clearError ? null : error ?? this.error,
      busyIds: busyIds ?? this.busyIds,
    );
  }
}
