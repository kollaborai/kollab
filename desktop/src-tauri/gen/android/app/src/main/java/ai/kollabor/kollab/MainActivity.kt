package ai.kollabor.kollab

import android.graphics.Color
import android.os.Bundle
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge

class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    // The PhoneLink page is dark: light status bar icons even in light mode, as on iOS (#369).
    enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT))
    super.onCreate(savedInstanceState)
  }
}
