procedure Body;
var D, B, It, C: Variant;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eComponentObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  C := It.FirstPCBObject;
  while C <> Nil do
  begin
    C.Selected := (C.Name.Text = 'C24');
    C := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  B.CurrentLayer := eBottomLayer;
  ResetParameters; AddStringParameter('Action', 'Selected'); RunProcess('PCB:Zoom');
  L('ok');
end;
