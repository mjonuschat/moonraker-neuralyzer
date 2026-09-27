.PHONY: install.sh

# Regenerates install.sh by concatenating the vendored kpi.sh library
# with scripts/install.logic.sh, so install.sh has no runtime file
# dependencies and works both as a local script and via curl | bash.
install.sh: scripts/kpi.sh scripts/install.logic.sh
	cat scripts/kpi.sh scripts/install.logic.sh > install.sh
	chmod +x install.sh
