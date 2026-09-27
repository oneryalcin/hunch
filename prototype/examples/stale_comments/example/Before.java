class Example {

	/**
	 * @return org.parosproxy.paros.view.HttpPanel
	 */
	public HttpPanel getResponsePanel() {
		if (responsePanel == null) {
			responsePanel = new HttpPanel(false);
		}
		return responsePanel;
	}

	/**
	 * Checks if toolitem is selected
	 * @param w given widget
	 */
	public boolean isSelected(final ToolItem w) {
		boolean selectionState = Display.syncExec(new ResultRunnable<Boolean>() {
			@Override
			public Boolean run() {
					return w.getSelection(); 
			}
		});
		return selectionState;
	}
}
