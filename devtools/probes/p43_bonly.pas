procedure Body;
var D, B, It, C: Variant; Lyr: Integer;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  for Lyr := 1 to 82 do
    try B.LayerIsDisplayed[Lyr] := (Lyr = eBottomLayer) or (Lyr = eMultiLayer); except end;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eComponentObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  C := It.FirstPCBObject;
  while C <> Nil do
  begin
    C.Selected := (C.Name.Text = 'C24') or (C.Name.Text = 'C26') or (C.Name.Text = 'C23');
    C := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  ResetParameters; AddStringParameter('Action', 'Selected'); RunProcess('PCB:Zoom');
  B.ViewManager_FullUpdate;
  ResetParameters; AddStringParameter('Action', 'All'); RunProcess('PCB:DeSelect');
  L('ok');
end;
