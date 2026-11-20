procedure Body;
var D, B, It, O, N, P: Variant; S: String;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do
  begin
    S := 'poly ' + O.Name + ' inNet=' + BoolToStr(O.InNet, True);
    if O.Net <> Nil then S := S + ' net=' + O.Net.Name else S := S + ' net=NIL';
    S := S + ' pourover=' + IntToStr(O.PourOver) + ' poured=' + BoolToStr(O.Poured, True);
    L(S);
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  { try assigning a net to the first polygon and read it back }
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eNetObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  N := It.FirstPCBObject;
  B.BoardIterator_Destroy(It);
  L('first net ' + N.Name);
end;
