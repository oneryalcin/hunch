class Example {

	/**
	 * @return org.parosproxy.paros.view.HttpPanel
	 */
	private HttpPanelResponse getResponsePanel() {
		if (responsePanel == null) {
			responsePanel = new HttpPanelResponse(false, extension, httpMessage);
		}
		return responsePanel;
	}

	/**
	 * Checks if toolitem is selected
	 * @param w given widget
	 */
	public boolean isSelected(final ToolItem toolItem) {
		boolean selectionState = Display.syncExec(new ResultRunnable<Boolean>() {
			@Override
			public Boolean run() {
					return toolItem.getSelection(); 
			}
		});
		return selectionState;
	}
}
