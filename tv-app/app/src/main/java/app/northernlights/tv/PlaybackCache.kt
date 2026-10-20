package app.northernlights.tv

import android.content.Context
import androidx.media3.database.StandaloneDatabaseProvider
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.datasource.cache.LeastRecentlyUsedCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import java.io.File

/** Shared bounded disk cache for TV playback; one cache instance must own its directory. */
internal object PlaybackCache {
    private const val MAX_CACHE_BYTES = 512L * 1024L * 1024L
    private const val DIRECTORY_NAME = "playback-cache"

    @Volatile
    private var cache: SimpleCache? = null

    fun dataSourceFactory(context: Context): DataSource.Factory {
        val appContext = context.applicationContext
        val diskCache = cache ?: synchronized(this) {
            cache ?: SimpleCache(
                File(appContext.cacheDir, DIRECTORY_NAME),
                LeastRecentlyUsedCacheEvictor(MAX_CACHE_BYTES),
                StandaloneDatabaseProvider(appContext),
            ).also { cache = it }
        }

        val upstream = DefaultDataSource.Factory(
            appContext,
            DefaultHttpDataSource.Factory().setAllowCrossProtocolRedirects(true),
        )
        return CacheDataSource.Factory()
            .setCache(diskCache)
            .setUpstreamDataSourceFactory(upstream)
            .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
    }
}
