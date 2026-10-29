// Release builds on Windows get no console window.
#![cfg_attr(all(not(debug_assertions), windows), windows_subsystem = "windows")]

fn main() {
    kollab_desktop_lib::run();
}
