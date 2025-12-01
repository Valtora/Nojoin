use log::{info, warn};
use crate::notifications;

const GITHUB_REPO: &str = "Valtora/Nojoin";
const CURRENT_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(serde::Deserialize)]
struct GitHubRelease {
    tag_name: String,
    html_url: String,
}

fn parse_version(version: &str) -> Option<(u32, u32, u32)> {
    let v = version.trim_start_matches('v');
    let parts: Vec<&str> = v.split('.').collect();
    if parts.len() >= 3 {
        let major = parts[0].parse().ok()?;
        let minor = parts[1].parse().ok()?;
        let patch = parts[2].parse().ok()?;
        Some((major, minor, patch))
    } else if parts.len() == 2 {
        let major = parts[0].parse().ok()?;
        let minor = parts[1].parse().ok()?;
        Some((major, minor, 0))
    } else {
        None
    }
}

fn is_newer_version(current: &str, latest: &str) -> bool {
    match (parse_version(current), parse_version(latest)) {
        (Some((c_major, c_minor, c_patch)), Some((l_major, l_minor, l_patch))) => {
            if l_major > c_major {
                return true;
            }
            if l_major == c_major && l_minor > c_minor {
                return true;
            }
            if l_major == c_major && l_minor == c_minor && l_patch > c_patch {
                return true;
            }
            false
        }
        _ => false,
    }
}

async fn fetch_latest_release() -> Option<GitHubRelease> {
    let client = reqwest::Client::builder()
        .user_agent("NojoinCompanion")
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .ok()?;
    
    let url = format!("https://api.github.com/repos/{}/releases/latest", GITHUB_REPO);
    
    match client.get(&url).send().await {
        Ok(response) => {
            if response.status().is_success() {
                response.json::<GitHubRelease>().await.ok()
            } else {
                warn!("GitHub API returned status: {}", response.status());
                None
            }
        }
        Err(e) => {
            warn!("Failed to fetch latest release: {}", e);
            None
        }
    }
}

pub async fn check_for_updates() {
    info!("Checking for updates (current version: {})...", CURRENT_VERSION);
    
    if let Some(release) = fetch_latest_release().await {
        let latest_version = release.tag_name.trim_start_matches('v');
        
        if is_newer_version(CURRENT_VERSION, latest_version) {
            info!("New version available: {} (current: {})", latest_version, CURRENT_VERSION);
            notifications::show_notification(
                "Update Available",
                &format!("Nojoin Companion {} is available. Click 'Check for Updates' in the menu to download.", latest_version)
            );
        } else {
            info!("Already on the latest version ({})", CURRENT_VERSION);
        }
    }
}

pub async fn check_for_updates_interactive() {
    info!("Manual update check triggered...");
    
    if let Some(release) = fetch_latest_release().await {
        let latest_version = release.tag_name.trim_start_matches('v');
        
        if is_newer_version(CURRENT_VERSION, latest_version) {
            info!("New version available: {}", latest_version);
            notifications::show_notification(
                "Update Available",
                &format!("Nojoin Companion {} is available!", latest_version)
            );
            // Open the releases page
            let _ = open::that(&release.html_url);
        } else {
            notifications::show_notification(
                "No Updates Available",
                &format!("You are on the latest version ({}).", CURRENT_VERSION)
            );
        }
    } else {
        notifications::show_notification(
            "Update Check Failed",
            "Could not check for updates. Please try again later."
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version_comparison() {
        assert!(is_newer_version("0.1.0", "0.1.1"));
        assert!(is_newer_version("0.1.0", "0.2.0"));
        assert!(is_newer_version("0.1.0", "1.0.0"));
        assert!(!is_newer_version("0.1.0", "0.1.0"));
        assert!(!is_newer_version("0.2.0", "0.1.0"));
        assert!(is_newer_version("0.1.0", "v0.2.0"));
    }
}


