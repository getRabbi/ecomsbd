import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';

import '../../core/api/api_error.dart';
import 'models.dart';
import 'repository_support.dart';

/// One screen's worth of a cursor-paginated list.
@immutable
class PagedListState<T> {
  const PagedListState({
    this.items = const <Never>[],
    this.isLoading = false,
    this.isLoadingMore = false,
    this.hasMore = false,
    this.nextCursor,
    this.error,
    this.origin = DataOrigin.network,
    this.fetchedAt,
  });

  final List<T> items;

  /// First load or refresh. Drives the skeleton.
  final bool isLoading;

  final bool isLoadingMore;
  final bool hasMore;
  final String? nextCursor;

  /// The last failure. Kept alongside the items rather than replacing them:
  /// losing a connection should not blank a list the seller was reading.
  final ApiError? error;

  final DataOrigin origin;

  /// When this data was true. Shown with cached results (master spec
  /// section 126).
  final DateTime? fetchedAt;

  bool get isEmpty => items.isEmpty && !isLoading;
  bool get isStale => origin == DataOrigin.cache;

  PagedListState<T> copyWith({
    List<T>? items,
    bool? isLoading,
    bool? isLoadingMore,
    bool? hasMore,
    String? nextCursor,
    ApiError? error,
    bool clearError = false,
    DataOrigin? origin,
    DateTime? fetchedAt,
  }) {
    return PagedListState<T>(
      items: items ?? this.items,
      isLoading: isLoading ?? this.isLoading,
      isLoadingMore: isLoadingMore ?? this.isLoadingMore,
      hasMore: hasMore ?? this.hasMore,
      nextCursor: nextCursor,
      error: clearError ? null : (error ?? this.error),
      origin: origin ?? this.origin,
      fetchedAt: fetchedAt ?? this.fetchedAt,
    );
  }
}

/// Shared paging behaviour for the commerce list screens.
///
/// Subclasses supply [fetchPage]; this class owns the state machine — first
/// load, refresh, append, and the debounce that stops a search box from firing
/// a request per keystroke on a connection where each one costs real time.
abstract class PagedListController<T> extends StateNotifier<PagedListState<T>> {
  PagedListController() : super(const PagedListState<Never>());

  static const Duration searchDebounce = Duration(milliseconds: 300);

  Timer? _debounce;
  int _generation = 0;

  /// Fetch one page. `cursor` is null for the first.
  Future<Sourced<PagedResult<T>>> fetchPage({String? cursor});

  Future<void> refresh() async {
    final generation = ++_generation;
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final result = await fetchPage();
      if (!mounted || generation != _generation) {
        return;
      }
      state = PagedListState<T>(
        items: result.value.items,
        hasMore: result.value.hasMore,
        nextCursor: result.value.nextCursor,
        origin: result.origin,
        fetchedAt: result.fetchedAt,
        error: result.error,
      );
    } on ApiError catch (error) {
      if (!mounted || generation != _generation) {
        return;
      }
      state = state.copyWith(isLoading: false, error: error);
    }
  }

  Future<void> loadMore() async {
    if (state.isLoadingMore || !state.hasMore || state.nextCursor == null) {
      return;
    }
    final generation = _generation;
    state = state.copyWith(isLoadingMore: true);
    try {
      final result = await fetchPage(cursor: state.nextCursor);
      if (!mounted || generation != _generation) {
        return;
      }
      state = state.copyWith(
        items: <T>[...state.items, ...result.value.items],
        isLoadingMore: false,
        hasMore: result.value.hasMore,
        nextCursor: result.value.nextCursor,
        origin: result.origin,
        fetchedAt: result.fetchedAt,
      );
    } on ApiError catch (error) {
      if (!mounted || generation != _generation) {
        return;
      }
      state = state.copyWith(isLoadingMore: false, error: error);
    }
  }

  /// Re-run the query after a short pause, for search-as-you-type.
  void refreshDebounced() {
    _debounce?.cancel();
    _debounce = Timer(searchDebounce, refresh);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }
}
